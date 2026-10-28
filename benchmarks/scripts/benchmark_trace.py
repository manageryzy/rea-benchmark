"""Opt-in Chrome Trace profiler. Records names/timing only, never frame locals."""
import argparse
from collections import defaultdict
from contextlib import ExitStack, nullcontext, contextmanager
from datetime import datetime, timezone
import functools
import importlib
import inspect
import json
import os
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCOPES = {
    'run.py': {'plan', 'selected_entries', 'run_batch', 'prepare_next_cell', 'prepare_task', 'launch_model', 'execute', 'grade_task', 'finalize', '_paged_tools'},
    'startup_policy.py': {'pinned_runtime'},
    'grading/runtime.py': {'prepared_image', 'extract', 'image_signature', 'invoke'},
    'benchmark_config.py': {'load'},
    'capabilities/config.py': {'load'},
    'capabilities/coordinator.py': {'entries', 'inspect_selected', 'resolve_selected', 'prepare_selected', 'require_case_validation', '_require_case_validation', 'public_inputs', '_public_inputs', 'prepare'},
    'environment_profile.py': {'current'},
    'capabilities/evidence.py': {'require_validation'},
    'validation_cache.py': {'__init__', 'declared_inputs', 'host_tools', 'project', 'lookup', 'require'},
    'incremental_validation.py': {'build', 'baseline', 'compare_inputs', 'require'},
    'capabilities/analysis_context.py': {'require_validation', 'public_catalog'},
    'capabilities/preparation.py': {'inspect', 'action', 'prepare'},
    'capabilities/tasks.py': {'declared', 'frozen', 'pin_snapshot'},
    'capabilities/registration.py': {'begin', 'complete', 'seal', 'require_validation', 'context', 'scoring_state', '_scoring_state', 'publish', 'verify_task'},
    'preparation_session.py': {'once', 'remember_task', 'resolved_task', 'require_context', 'scoring'},
    'runtime_attestation.py': {'current_runtime', 'capture', 'validate', 'component_blobs', '_component_blobs', 'component_entries', 'tree', 'file', 'shared_libraries', 'verify', 'verify_configuration', 'provider'},
    'runtime_snapshot.py': {'current', 'populate', 'reuse', 'active'},
    'cache.py': {'action_input_bytes', 'profile_bytes', 'inference_identity'},
    'action_cache.py': {'canonical', 'digest', '__init__', 'lookup', 'publish', 'restore', 'commit', 'get', 'put'},
    'read_session.py': {'clone_cache_value', 'freeze_cache_value', 'return_cache_value',
                        'observe', 'cas_get', 'verify_inspection'},
    'records.py': {'frozen_cases', 'collect', 'condition_records'},
    'native_case_registry.py': {'configuration', 'require_matrix', 'extensions', 'public_directory'},
    'native_attempts.py': {'route', 'select', 'report_directories'},
    'capabilities/store.py': {'spec', 'fixed_inputs', 'prepare', 'read_selected', 'grade'},
    'grading/store.py': {'identity', 'read_selected', 'grade'},
    'report.py': {'build', 'public', 'pages'},
}
RUNTIME_SCOPES = {'runtime_storage.py': {
    'settings', 'is_wsl', 'windows_mount', 'automatic', 'execute', 'package',
    'pack_installed', 'sha_file', 'reap_stale', 'guarded_process', 'preserve_inventories'},
    'runtime_image.py': {'policy', 'cache_root', 'facts', 'verified_image', 'load',
        'native_libraries', 'capture_system', 'build', 'fuse_available', 'unmount',
        'extracted_image', 'check_manifest', 'container_command', 'execute', 'prune_legacy', 'copy_public_tree'}}


def traced(name=None, *, recorder=None, min_duration_ms=0):
    """Decorate a critical path without observing arguments, locals or results."""
    def decorate(function):
        label = name or function.__module__ + ':' + function.__qualname__
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            if recorder is None:
                return function(*args, **kwargs)
            now, tid = time.perf_counter_ns(), threading.get_ident()
            if not min_duration_ms:
                with recorder.lock:
                    recorder.event(label, 'B', tid, now)
            try:
                return function(*args, **kwargs)
            finally:
                end = time.perf_counter_ns()
                with recorder.lock:
                    recorder.totals[label][0] += 1
                    recorder.totals[label][1] += end - now
                    if not min_duration_ms:
                        recorder.event(label, 'E', tid, end)
                    elif end - now >= min_duration_ms * 1e6:
                        recorder.event(label, 'X', tid, now, duration=end - now)
        return wrapped
    return decorate


class Trace:
    def __init__(self, path, *, detail=False, render_html=True, scopes=None,
                 epoch_ns=None, process_id=1, runtime_io=False):
        self.detail = detail
        self.render_html = render_html
        self.scopes = SCOPES if scopes is None else scopes
        self.process_id = process_id
        self.runtime_io = runtime_io
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.opened = time.perf_counter_ns()
        self.started = self.opened if epoch_ns is None else epoch_ns
        self.lock = threading.RLock()
        self.totals = defaultdict(lambda: [0, 0])
        self.stream = self.path.open('x')
        self.stream.write('{"traceEvents":[\n')
        self.first = True
        self.last_flush = 0

    def event(self, name, phase, tid, now, duration=None):
        row = {'name': name, 'cat': 'benchmark', 'ph': phase,
               'ts': (now - self.started) / 1000, 'pid': self.process_id, 'tid': tid}
        if duration is not None:
            row['dur'] = duration / 1000
        self.stream.write(('' if self.first else ',\n') + json.dumps(row))
        self.first = False
        # Flushing every metadata event on /mnt/* makes the profiler itself a
        # synchronous IO bottleneck. Keep a bounded live tail; close flushes all.
        stamp = time.perf_counter_ns()
        if stamp - self.last_flush >= 250_000_000:
            self.stream.flush()
            self.last_flush = stamp

    def __enter__(self):
        self.patches = ExitStack()
        try:
            self._install()
        except BaseException:
            self.patches.close()
            self.stream.close()
            raise
        return self

    def _install(self):
        for relative, names in self.scopes.items():
            with self.span('startup:instrument_import:' + relative):
                module = importlib.import_module(relative[:-3].replace('/', '.'))
            owners = [module] + [value for value in vars(module).values()
                if inspect.isclass(value) and value.__module__ == module.__name__]
            for owner in owners:
                for name in names:
                    value = vars(owner).get(name)
                    if not inspect.isfunction(value):
                        continue
                    label = relative + ':' + value.__qualname__
                    self.patches.enter_context(patch.object(owner, name,
                        traced(label, recorder=self,
                               min_duration_ms=1 if name in ('canonical', 'digest', 'declared', 'load', 'clone_cache_value', 'return_cache_value', 'observe', 'cas_get') else (20 if name in ('file', 'get', 'put') else 0))(value)))
        if self.runtime_io:
            self._runtime_probes()
        if self.detail:
            # Opt-in leaf attribution; no path names, arguments or data are recorded.
            import hashlib
            import read_session
            leaves = [(Path, 'read_bytes', 'io:Path.read_bytes'),
                      (Path, 'resolve', 'metadata:Path.resolve'),
                      (os, 'lstat', 'metadata:os.lstat'),
                      (hashlib, 'sha256', 'hash:sha256'),
                      (read_session, 'loaded_code', 'guard:loaded_code'),
                      (read_session, 'code_current', 'guard:code_current'),
                      (read_session, 'semantic_context', 'guard:semantic_context'),
                      (read_session, 'signature', 'guard:stat_signature'),
                      (read_session.Reads, 'merge', 'guard:Reads.merge'),
                      (read_session.ReadSession, 'read_bytes', 'cache:stat_keyed_read_bytes'),
                      (read_session.Reads, 'current', 'guard:Reads.current'),
                      (read_session.Reads, 'remember', 'guard:Reads.remember'),
                      (read_session.ReadSession, 'record_path', 'guard:record_path')]
            for owner, name, label in leaves:
                self.patches.enter_context(patch.object(owner, name,
                    traced(label, recorder=self, min_duration_ms=1)(getattr(owner, name))))
            module = importlib.import_module('grading.image_manifest')
            for key in ('load', 'validate_record', 'file_sha'):
                self.patches.enter_context(patch.object(module, key,
                    traced('grading/image_manifest.py:' + key, recorder=self)(getattr(module, key))))
            for name, keys in {
                'capabilities.baselines.task_runtime': ('require_validation', 'fixed_inputs'),
                'grading.c_executor.runtime': ('engine_record', 'platform'),
                'grading.c_executor.image': ('record',),
            }.items():
                module = importlib.import_module(name)
                for key in keys:
                    self.patches.enter_context(patch.object(module, key,
                        traced(name + ':' + key, recorder=self)(getattr(module, key))))
            for name in ('pointers', 'abi', 'variant_objects', 'indirect_calls',
                         'native_abi', 'optimized_locations', 'baselines'):
                module = importlib.import_module('capabilities.' + name + '.tasks')
                for key in ('validate_frozen', 'validate_original_binding', 'validate_original', 'validate_private'):
                    if hasattr(module, key):
                        self.patches.enter_context(patch.object(module, key,
                            traced(module.__name__ + ':' + key, recorder=self)(getattr(module, key))))
        if self.detail:
            self._leaf_probes()

    @contextmanager
    def span(self, label):
        now, tid = time.perf_counter_ns(), threading.get_ident()
        with self.lock:
            self.event(label, 'B', tid, now)
        try:
            yield
        finally:
            end = time.perf_counter_ns()
            with self.lock:
                self.totals[label][0] += 1
                self.totals[label][1] += end - now
                self.event(label, 'E', tid, end)

    @contextmanager
    def instrument(self, scopes, *, detail=False):
        previous = self.patches, self.scopes, self.detail
        self.patches, self.scopes, self.detail = ExitStack(), scopes, detail
        try:
            with self.span('startup:install_probes'):
                self._install()
            yield self
        finally:
            self.patches.close()
            self.patches, self.scopes, self.detail = previous

    def _leaf_probes(self):
        # Read the actual buffered stream separately from Path.open's metadata
        # guards. No file names or bytes are recorded, including credentials.
        import read_session
        self.patches.enter_context(patch.object(read_session, 'buffer_read',
            traced('io:buffer.read', recorder=self, min_duration_ms=1)(read_session.buffer_read)))
        if not getattr(Path.read_bytes, '__rea_bytecache__', False):
            def read_bytes(path):
                with path.open('rb') as stream:
                    return traced('io:buffer.read', recorder=self, min_duration_ms=1)(stream.read)()
            self.patches.enter_context(patch.object(Path, 'read_bytes',
                traced('io:Path.read_bytes', recorder=self, min_duration_ms=1)(read_bytes)))
        import copy
        original_copy = copy.deepcopy
        copying = threading.local()
        @functools.wraps(original_copy)
        def deepcopy(*args, **kwargs):
            # Attribute only outer copies; recursive element copies must not
            # create tens of thousands of spans or expose object contents.
            if getattr(copying, 'active', False):
                return original_copy(*args, **kwargs)
            caller = sys._getframe(1).f_code
            filename = caller.co_filename.replace('\\', '/')
            marker = '/benchmarks/scripts/'
            label = filename.split(marker, 1)[-1] if marker in filename else '<external>'
            copying.active = True
            try:
                return traced('copy:deepcopy:' + label + ':' + caller.co_name,
                              recorder=self, min_duration_ms=1)(original_copy)(*args, **kwargs)
            finally:
                copying.active = False
        self.patches.enter_context(patch.object(copy, 'deepcopy', deepcopy))
        import hashlib
        original_sha = hashlib.sha256
        recorder = self
        class Hash:
            def __init__(self, value): self.value = value
            @traced('hash:sha256.update', recorder=recorder, min_duration_ms=1)
            def update(self, data): return self.value.update(data)
            @traced('hash:sha256.digest', recorder=recorder, min_duration_ms=1)
            def digest(self): return self.value.digest()
            @traced('hash:sha256.hexdigest', recorder=recorder, min_duration_ms=1)
            def hexdigest(self): return self.value.hexdigest()
            def copy(self): return Hash(self.value.copy())
            def __getattr__(self, name): return getattr(self.value, name)
        @functools.wraps(original_sha)
        def sha(*args, **kwargs): return Hash(original_sha(*args, **kwargs))
        self.patches.enter_context(patch.object(hashlib, 'sha256', sha))

    def __exit__(self, *error):
        self.patches.close()
        with self.lock:
            self.stream.write('\n],"displayTimeUnit":"ms"}\n')
            self.stream.close()
        summary = {'elapsed_seconds': (time.perf_counter_ns() - self.opened) / 1e9,
                   'completed': error[0] is None,
                   'functions': sorted([{'name': name, 'calls': calls, 'inclusive_seconds': ns / 1e9}
                       for name, (calls, ns) in self.totals.items()],
                       key=lambda row: -row['inclusive_seconds'])}
        self.path.with_suffix('.summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        if self.render_html:
            render(self.path)

    def _runtime_probes(self):
        """Observe existing staging code without changing pinned source bytes."""
        import hashlib
        import fcntl
        import runtime_storage
        import tarfile
        import tempfile
        import runtime_image
        recorder = self
        def observe_context(original, label):
            @functools.wraps(original)
            def temporary(*args, **kwargs):
                context = original(*args, **kwargs)
                class ObservedContext:
                    @traced(label + '.enter', recorder=recorder)
                    def __enter__(self): return context.__enter__()
                    @traced(label + '.exit_cleanup', recorder=recorder)
                    def __exit__(self, *error): return context.__exit__(*error)
                return ObservedContext()
            return temporary
        for module, name in ((runtime_storage, 'temporary_runtime'),
                             (runtime_image, 'image_root'), (runtime_image, 'fuse_mount')):
            original = getattr(module, name)
            self.patches.enter_context(patch.object(module, name,
                observe_context(original, module.__name__ + ':' + name)))
        for owner, name, label, threshold in (
                (tarfile.TarFile, 'extract', 'runtime_archive:extract_member', 20),
                (fcntl, 'flock', 'runtime_storage:file_lock', 0),
                (tempfile.TemporaryDirectory, 'cleanup', 'runtime_storage:temporary_directory_cleanup', 0)):
            self.patches.enter_context(patch.object(owner, name,
                traced(label, recorder=self, min_duration_ms=threshold)(getattr(owner, name))))
        original_iter = tarfile.TarFile.__iter__
        @functools.wraps(original_iter)
        def members(stream):
            # Generator lifetime includes all extraction work between yields.
            label, now, tid = 'runtime_archive:extract_members', time.perf_counter_ns(), threading.get_ident()
            with recorder.lock: recorder.event(label, 'B', tid, now)
            try:
                yield from original_iter(stream)
            finally:
                end = time.perf_counter_ns()
                with recorder.lock:
                    recorder.totals[label][0] += 1
                    recorder.totals[label][1] += end - now
                    recorder.event(label, 'E', tid, end)
        self.patches.enter_context(patch.object(tarfile.TarFile, '__iter__', members))
        original_open = Path.open
        class ArchiveStream:
            def __init__(self, stream, label):
                self.stream = stream
                self.read = traced(label + '.read', recorder=recorder, min_duration_ms=20)(stream.read)
                self.write = traced(label + '.write', recorder=recorder, min_duration_ms=20)(stream.write)
            def __enter__(self): self.stream.__enter__(); return self
            def __exit__(self, *error): return self.stream.__exit__(*error)
            def __getattr__(self, name): return getattr(self.stream, name)
        def opened(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            if Path(path).suffix == '.tar':
                label = 'runtime_archive:linux_snapshot' if Path(path).name == 'runtime.tar' else 'runtime_archive:source'
                return ArchiveStream(stream, label)
            return stream
        self.patches.enter_context(patch.object(Path, 'open', opened))
        original_sha = hashlib.sha256
        class Hash:
            def __init__(self, value):
                self.value = value
                self.update = traced('runtime_archive:sha256.update', recorder=recorder, min_duration_ms=20)(value.update)
            def __getattr__(self, name): return getattr(self.value, name)
        self.patches.enter_context(patch.object(hashlib, 'sha256', lambda *a, **k: Hash(original_sha(*a, **k))))


def trace_events(path):
    """Read complete events, retaining a killed worker's unfinished B spans."""
    raw = Path(path).read_text()
    try:
        return json.loads(raw)['traceEvents']
    except json.JSONDecodeError:
        start = raw.index('[', raw.index('"traceEvents"')) + 1
        decoder, events = json.JSONDecoder(), []
        while start < len(raw):
            while start < len(raw) and raw[start] in ' \t\r\n,': start += 1
            try: event, end = decoder.raw_decode(raw, start)
            except json.JSONDecodeError: break
            if not isinstance(event, dict): break
            events.append(event); start = end
        return events


def staged(command, output, *, detail=False):
    """Start tracing before staging; merge worker events on the same clock."""
    import runtime_storage
    epoch = time.perf_counter_ns()
    worker = output.with_name(output.stem + '.worker.json')
    arguments = [sys.executable, str(Path(__file__).resolve()), '--accelerate', '--no-render',
                 '--output', str(worker), '--epoch-ns', str(epoch), '--trace-pid', '2']
    if detail: arguments.append('--detail')
    arguments += ['--', *command]
    saved_argv = sys.argv
    try:
        with patch.dict(os.environ, {'REA_BENCHMARK_STARTED_NS': str(epoch)}), \
                Trace(output, scopes=RUNTIME_SCOPES, epoch_ns=epoch, runtime_io=True, render_html=False):
            sys.argv = ['runtime_storage.py', 'run', '--', *arguments]
            result = runtime_storage.main()
            if result: raise SystemExit(result)
    finally:
        sys.argv = saved_argv
        if output.exists() and worker.exists():
            events = trace_events(output) + trace_events(worker)
            events.sort(key=lambda event: event['ts'])
            runtime = output.with_name(output.stem + '.runtime.json')
            # Both original streams remain available; only this run's root is merged.
            output.rename(runtime)
            with output.open('x') as stream:
                json.dump({'traceEvents': events, 'displayTimeUnit': 'ms'}, stream)
            print('MERGED BENCHMARK TRACE: ' + str(output), flush=True)


def render(path):
    """Self-contained timing viewer; Chrome Trace stays available for Perfetto."""
    path = Path(path)
    raw = path.read_text()
    # Allow viewing the append-only stream while a profiled process is running.
    if not raw.rstrip().endswith('}') or '"displayTimeUnit"' not in raw:
        raw = raw.rstrip().rstrip(',') + '\n],"displayTimeUnit":"ms"}'
    events = json.loads(raw)['traceEvents']
    stacks, rows = defaultdict(list), []
    end = max((e['ts'] + e.get('dur', 0) for e in events), default=0)
    for e in events:
        stack = stacks[e['tid']]
        if e['ph'] == 'X':
            rows.append([e['name'], e['ts'] / 1e6, e['dur'] / 1e6, str(e['tid']), len(stack), False])
        elif e['ph'] == 'B':
            stack.append((e, len(stack)))
        elif e['ph'] == 'E' and stack:
            begin, depth = stack.pop()
            rows.append([begin['name'], begin['ts'] / 1e6, (e['ts'] - begin['ts']) / 1e6,
                         str(begin['tid']), depth, False])
    for stack in stacks.values():
        for begin, depth in stack:
            rows.append([begin['name'], begin['ts'] / 1e6, (end - begin['ts']) / 1e6,
                         str(begin['tid']), depth, True])
    payload = json.dumps(rows).replace('<', '\\u003c')
    html = '''<!doctype html><meta charset="utf-8"><title>Benchmark timing trace</title>
<style>body{font:14px system-ui;margin:24px;background:#111827;color:#e5e7eb}input,select{margin:8px;padding:6px}#chart{overflow:auto;border:1px solid #475569}svg{display:block}table{border-collapse:collapse}td,th{text-align:left;padding:6px 14px;border-bottom:1px solid #334155}a{color:#7dd3fc}</style>
<h1>Benchmark timing trace</h1><p>Wall-clock spans in seconds. Nested durations overlap: do not sum them. Hatched labels indicate unfinished calls. No prompts, credentials or model output are captured.</p>
<label>Filter <input id="filter" placeholder="function name"></label><label>Zoom <input id="zoom" type="range" min="1" max="30" value="1"></label><div id="chart"></div><h2>Inclusive time by function</h2><table id="summary"></table>
<script>const rows=PAYLOAD;
const escape=s=>s.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function draw(){const query=document.querySelector('#filter').value.toLowerCase(),scale=+document.querySelector('#zoom').value;
 const selected=rows.filter(r=>r[0].toLowerCase().includes(query)), tids=[...new Set(rows.map(r=>r[3]))];
 const lanes={};let height=40;for(const tid of tids){lanes[tid]=height;height+=26*(Math.max(0,...rows.filter(r=>r[3]===tid).map(r=>r[4]))+2);}
 const end=Math.max(1,...rows.map(r=>r[1]+r[2])),w=1200*scale;let svg=`<svg xmlns="http://www.w3.org/2000/svg" width="${w+100}" height="${height}">`;
 for(let i=0;i<=10*scale;i++){let x=50+i*w/(10*scale);svg+=`<line x1="${x}" y1="25" x2="${x}" y2="${height}" stroke="#334155"/><text x="${x}" y="18" fill="#cbd5e1">${(end*i/(10*scale)).toFixed(1)}s</text>`;}
 for(const r of selected){const x=50+w*r[1]/end,y=lanes[r[3]]+26*r[4],width=Math.max(1,w*r[2]/end),color=`hsl(${r[4]*43+r[0].length*7} 55% 43%)`;
 svg+=`<g><title>${escape(r[0])}\nstart ${r[1].toFixed(3)}s; duration ${r[2].toFixed(3)}s${r[5]?' (unfinished)':''}</title><rect x="${x}" y="${y}" width="${width}" height="23" fill="${color}" stroke="${r[5]?'#fbbf24':'#111827'}"/>${width>100?`<text x="${x+3}" y="${y+16}" font-size="11" fill="white">${escape(r[0].slice(-Math.floor(width/7)))}</text>`:''}</g>`;}
 document.querySelector('#chart').innerHTML=svg+'</svg>';const sums={};for(const r of selected){const s=sums[r[0]]??=[0,0,0];s[0]++;s[1]+=r[2];s[2]+=+r[5];}
 document.querySelector('#summary').innerHTML='<tr><th>Function</th><th>Calls</th><th>Inclusive seconds</th><th>Unfinished</th></tr>'+Object.entries(sums).sort((a,b)=>b[1][1]-a[1][1]).map(([n,s])=>`<tr><td>${escape(n)}</td><td>${s[0]}</td><td>${s[1].toFixed(3)}</td><td>${s[2]}</td></tr>`).join('');}
document.querySelector('#filter').oninput=draw;document.querySelector('#zoom').oninput=draw;draw();</script>'''
    path.with_suffix('.html').write_text(html.replace('PAYLOAD', payload))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--render', type=Path)
    parser.add_argument('--accelerate', action='store_true', help='Reuse successful read-only admission with mutation guards')
    parser.add_argument('--detail', action='store_true', help='Attribute IO, hashing and admission guard overhead')
    parser.add_argument('--no-render', action='store_true', help='Write Chrome Trace and summary without HTML')
    parser.add_argument('--staged', action='store_true', help='Trace runtime staging before starting the benchmark worker')
    parser.add_argument('--epoch-ns', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--trace-pid', type=int, default=1, help=argparse.SUPPRESS)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.render:
        render(args.render)
        return
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not args.output or not command:
        parser.error('--output and a Python script after -- are required')
    if str(args.output) == 'auto':
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
        args.output = ROOT / '.cache/benchmark-runtime/profiling' / f'benchmark-{stamp}-{os.getpid()}.trace.json'
    print('BENCHMARK TRACE: ' + str(args.output), flush=True)
    if args.staged:
        staged(command, args.output, detail=args.detail)
        return
    sys.argv = command
    sys.path.insert(0, str(Path(command[0]).resolve().parent))
    started = time.perf_counter()
    try:
        # Open before imports and ReadSession initialization: neither is free on
        # WSL. Install function probes inside the session so teardown restores
        # functions in reverse order and does not leak memoized wrappers.
        with Trace(args.output, scopes={}, render_html=not args.no_render,
                   epoch_ns=args.epoch_ns, process_id=args.trace_pid) as recorder:
            with recorder.span('startup:import_command'):
                module = importlib.import_module(Path(command[0]).stem)
            with recorder.span('startup:import_read_session'):
                from read_session import ReadSession
            with ExitStack() as lifecycle:
                with recorder.span('startup:ReadSession.enter'):
                    session = lifecycle.enter_context(ReadSession() if args.accelerate else nullcontext())
                with recorder.instrument(SCOPES, detail=args.detail):
                    try:
                        with recorder.span('command:main'):
                            result = module.main()
                            if isinstance(result, int) and result: raise SystemExit(result)
                    finally:
                        if session is not None:
                            print(f'READ SESSION: hits={session.hits} misses={session.misses} invalidations={session.invalidations}', flush=True)
                with recorder.span('shutdown:ReadSession.exit'):
                    lifecycle.close()
    finally:
        elapsed = time.perf_counter() - started
        print(f'BENCHMARK PROFILED COMMAND TOTAL: {elapsed:.3f}s', flush=True)
        if Path(command[0]).name == 'run.py' and len(command) > 1 and command[1] == 'run':
            if (ROOT / 'benchmarks/config/benchmark-status.json').is_file():
                from report_io import publish_timing
                publish_timing(elapsed)


if __name__ == '__main__':
    main()
